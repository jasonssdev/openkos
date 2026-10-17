# Delta for desktop-review

## ADDED Requirements

### Requirement: A Changed Decision Is Checked Again, Never Decided As It Was

A decision whose `fresh` is false MUST be shown with the notice "Something about
these pages changed since this was suggested. Check them again before you
decide." and MUST offer only **Check again** ("We'll look at these pages as they
are now. Checking doesn't change anything. You'll still decide.") and **Decide
later**; it MUST NOT offer an action its apply would refuse. Check again MUST
first refresh the decision queue (`POST /v1/queue/refresh`, a failure ignored)
and list To review again, which calls no AI model. When the decision is no
longer listed, the app MUST say "These pages changed, and this question no
longer applies." and return to the list; when it is listed and no longer
changed, the app MUST show it as it now is, with its own options; when it is
still changed, the app MUST quote the decision's kind and show the check's
consent, and MUST NOT start a run until the person agrees. The app MUST refresh
the decision queue on each visit to To review, before it lists the decisions.

#### Scenario: A changed pair offers only Check again and Decide later

- GIVEN a judged-same pair whose `fresh` is false
- WHEN it is opened
- THEN its options are Check again and Decide later, and no combine is offered

#### Scenario: Check again makes a pair decidable without a check

- GIVEN a changed pair that the queue refresh makes fresh
- WHEN Check again is chosen
- THEN the queue is refreshed, the decisions are listed again, and the pair is
  shown with Combine them, and no quote is requested

#### Scenario: A still-changed pair asks for agreement first

- GIVEN a changed pair that is still changed after the queue refresh
- WHEN Check again is chosen
- THEN an identity quote is requested and the consent is shown, and no run is
  started

#### Scenario: A pair naming a missing page disappears

- GIVEN a changed pair that the queue refresh retires
- WHEN Check again is chosen
- THEN the app says "These pages changed, and this question no longer applies."
  and shows To review

#### Scenario: A visit refreshes before listing

- GIVEN the person opens To review
- WHEN the screen loads
- THEN the queue refresh is sent before the decisions are listed

## MODIFIED Requirements

### Requirement: A Stale, Busy Or Refused Choice Is Never Applied Blindly

A write refused as busy (`workspace_busy`) MUST be retried with the client's
bounded retry and, when that gives up, MUST say "OpenKOS is finishing something
else. Nothing was changed." with Try again. A stale preview or apply (`stale`,
`write_drift`) MUST reload To review and, when the decision is still open and
not changed, preview the same choice again and show "Something about these pages
changed since this was suggested. Here is the current version." without
applying; when it is still open but now changed (`fresh` false), the app MUST
show it as a changed decision, with Check again and Decide later, and MUST NOT
preview it again; when it is gone, the app MUST say so and return to the list.
Every refusal the brief's table names MUST be shown with its message and its one
action; an interrupted group combine MUST say "Only some of these were combined.
Nothing else changed, and each combine can be split again." and reload. A
failure the brief does not name MUST be worded without the engine's text, a code
or a command, and after a write MUST NOT claim nothing changed.

#### Scenario: A stale apply is re-previewed, not applied

- GIVEN a previewed choice whose apply answers `stale`, and a decision still
  fresh when listed again
- WHEN the answer arrives
- THEN the decisions are listed again and the same choice is previewed again
- AND the changed notice is shown and no second apply is sent

#### Scenario: A stale apply whose decision changed is not previewed again

- GIVEN a previewed choice whose apply answers `stale`, and a decision listed
  again with `fresh` false
- WHEN the answer arrives
- THEN no second preview is sent and the decision offers Check again

#### Scenario: Busy past the retry offers Try again

- GIVEN an apply that answers `workspace_busy` past the retry deadline
- WHEN it gives up
- THEN the message is "OpenKOS is finishing something else. Nothing was
  changed." with Try again, and Try again sends the same apply

### Requirement: The Check Is Agreed To Once Per Batch And Can Be Stopped

Check these, Check again, and Check all on the list when any identity decision
is unjudged or changed, MUST first refresh the decision queue
(`POST /v1/queue/refresh`, a failure ignored) and list To review again, then
quote (`POST /v1/judging/quote`, kind `identity`, or the changed decision's kind
for Check again) and show one consent for the batch: "Check {n} pairs?" ("Check
these two?" for one; "Check {n} connections?" or "Check this connection?" for
connections; "Check {n} suggestions?" or "Check this suggestion?" for how
quickly something goes out of date), where the AI model runs (on this Mac, or
the provider sentence when the binding is not local), how many are already
checked, the background line when the engine reports unattended work, and
"Checking never combines anything. You'll still decide each one." The run MUST
start only when the person agrees, with the quote's token echoed unchanged.
While it runs the app MUST show "Checking {done} of {n}…" with Stop, and Stop
MUST ask the engine to stop and say "Stopping after the current one. Everything
already checked is kept." When it ends the app MUST refresh the decision queue
(a failure ignored), say "Checked {n}. {m} are ready for you to decide." and
reload To review. A `quote_stale` answer MUST show "Something changed since you
agreed — the AI model or the pages. Please look again." and quote again; the run
MUST NOT start on the old quote. The app MUST never ask per item to spend.

#### Scenario: Nothing runs without agreement

- GIVEN an unjudged identity decision
- WHEN Check these is chosen
- THEN a quote is requested and the consent is shown
- AND no run is started until Check {n} is chosen

#### Scenario: A stale quote is asked again

- GIVEN a consent whose run answers `quote_stale`
- WHEN the answer arrives
- THEN the changed sentence is shown, a new quote is requested and no run is
  started

#### Scenario: A finished check publishes its verdicts

- GIVEN a check run that ends
- WHEN the app follows it to the end
- THEN the queue refresh is sent before To review is listed again

#### Scenario: Check all is offered for a changed pair

- GIVEN To review whose only identity decision is judged but changed
- WHEN the list is shown
- THEN Check all is offered
