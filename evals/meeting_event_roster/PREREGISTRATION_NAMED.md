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
are at least 10 and half the non-blank lines. The roster rule proposed here
borrows that shape and is **not** coupled to the private function; in
production it would run only on sources already judged meeting-shaped. A title-only
match (a summarised note) has no turns and so gets no roster, correctly.

## The proposed rule (`roster_of_named`, frozen here)

`roster_of` is untouched (it stays the registered rule for the AMI sources). The
new function returns, in order of first appearance, the union of:

1. **AMI-form labels**: exactly what `roster_of` returns (2 to 4 capitals or
   digits, at least 2 lines).
2. **Name-form labels**: a line opening, after an optional timestamp prefix and
   optional bold markers, with a label then `:` and a space and a non-space
   character, where the label is:
   - one to four capitalised words (proposed), each word starting with a
     capital (accents allowed), containing at least one lowercase letter and
     ending in one (so an all-capitals label is only ever the AMI form), with
     `.` allowed after a word (`Dr.`) or a single initial (`J.`), and
     apostrophes and hyphens inside it (`O'Neil`, `Jean-Luc`);
   - lowercase particles `de del la las los van von der da di le el` allowed
     between words (`Ana de la Cruz`);
   - **no digits**, so `Reunión 10:30:` and a timestamp-looking label fail;
   - **not a heading**: neither the whole label nor its first word is in the
     closed list `NOT_A_SPEAKER` (`Nota`, `Acción`, `Agenda`, `Asistentes`,
     `Fecha`, `Hora`, `Resumen`, `Pregunta`, `Respuesta`, `Acuerdos` and their
     English forms, about 50 words, in the harness). This is a lexicon, which
     production's detector avoids; it is disclosed as the one place this rule is
     not structural, and recurrence alone cannot separate `Acción:` repeated
     from a speaker.
   - It counts only on **at least 2 lines** (`ROSTER_MIN_TURNS`, as registered),
     and name-form labels are admitted only when the AMI-form and name-form
     labels together number **at least 2** speakers (a single repeated label is a
     monologue or a heading, not a conversation).
3. A URL (`https://...`: no space after the colon), a bullet or quote line
   (`- Ana:`, `> Ana:`), a lowercase or sentence-length prefix, and a label
   with a trailing colon only fail the line regex.

Known limit, not fixed here: because (1) is the registered rule unchanged, an
all-capitals log severity (`INFO:`, `ERROR:`) that recurs is still read as a
speaker, as it is today. Production's detector rejects those; matching it would
change the AMI rule, which this registration forbids.

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
| **Synthetic fixtures** | 14 positive and 7 negative sources written in the harness (`NAMED_FIXTURES`, `NAMED_NEGATIVES`), fictional names: plain, titles, compound names, timestamps, bold, particles, initials, apostrophes and hyphens, accents, a header block and repeated `Nota:`/`Acción:` lines inside a transcript, a stray single label, URLs and time lines, AMI form, mixed AMI and named; negatives: header block only, repeated headings, Q and A, a monologue, prose with colons, bullets and quotes, no labels | precision, recall, false labels |
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

Model-free: zero GPU, a few seconds. The self-test adds 7 tests.

## Disclosed before this registration (forking paths)

- The rule and the fixtures were written **together, by the same author, in the
  same sitting**; the self-test passes on all of them. The fixtures are therefore
  not blind: the rule was shaped by the classes the fixtures name (and by the
  failures the author thought of: headings, URLs, times, bullets). One rule
  change was made after a fixture failed (the speaker floor counts AMI-form and
  name-form labels together, found on `mixed_ami_and_named`). Pass on the
  fixtures is thus weak evidence; the harder, less-tuned evidence is medium-10
  and the 5 AMI sources, and even those were looked at.
- The author read the first 25 lines of medium-10 and the speaker-label counts of
  all 12 AMI translations (`PM ID UI ME` only, hundreds of turns each) and the
  head of one `ask-screen` meeting, to design the guards. `named` was **not**
  run, so no N5 or N6 number has been seen. N6 may fail for a reason the author
  has not seen (a capitalised word followed by a colon inside AMI utterances).
- The `NOT_A_SPEAKER` list was chosen by the author from common Spanish and
  English meeting headings, not from any corpus.
- The thresholds (four words, recurrence 2, two speakers) are the author's, not
  tuned on data; recurrence 2 is `ROSTER_MIN_TURNS`, as registered.

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
- A speaker who really speaks as `Acción:` or `Nota:` is lost by the list.
- All-caps log severities still read as speakers (above).
- Roster extraction only; whether the model then uses the list well on named
  speakers is the unmeasured expectation above.

## The owner confirms

Nothing below is confirmed yet.

1. **The rule**: the name-form label grammar (one to four capitalised words,
   titles, initials, particles, no digits), recurrence 2, the speaker floor of 2
   counting AMI-form labels, and the union with `roster_of` unchanged.
2. **The `NOT_A_SPEAKER` list** as the one disclosed lexicon (accepting a
   lexicon where production's detector uses none).
3. **The data**: synthetic fixtures plus medium-10 plus the 5 AMI sources, no
   `ask-screen` meetings, and the plain statement that no real named-speaker
   corpus exists locally.
4. **Bars N1 to N6** and the verdict (LICENSED only if all hold; REJECTED
   otherwise, with the N2, N3, N5 nuance).
5. **No model arm** in this registration, with the optional minimal arm
   (about 0.1 GPU-hours, cap 1 GPU hour) left to its own registration if wanted.
6. **The known limit** (all-caps log severities remain readable as speakers
   because the AMI rule is frozen) is accepted.
7. **Disclosure**: fixtures and rule were written together; medium-10 and the AMI
   label counts were looked at; `named` was not run.
8. **The closing rule** as written.
