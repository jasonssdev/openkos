# Delta for desktop-review

## MODIFIED Requirements

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

A changed `contradiction` or `revision` MUST be shown with "Something about
these pages changed since this was found, so it can't be settled as it was.
Check them again before you decide." and MUST offer **Check again** and
**Decide later**. Check again MUST refresh the queue and list To review as for
any changed decision and, when the decision is still changed, MUST quote the
one pair behind it (`POST /v1/judging/quote` with its `kind` and its
`decision_key`, never the batch) and show a consent for that pair, "Check these
two again?", whose last line says the decision leaves To review when the pages
no longer need one. A quote with nothing to judge MUST say "These can't be
checked again right now." and offer no agreement. Agreeing MUST start a run
bound to that quote and, when it ends, refresh the queue and list again; the
result MUST say that the decision is ready to decide, that it is no longer in
To review, or that the pages changed again meanwhile. A stale quote or a retry
after a failed quote MUST quote the same decision again. A changed finding MUST
NOT be counted as waiting for the batch check, so a batch check's "ready" count
never includes one.

#### Scenario: A changed contradiction is checked again for its one pair

- GIVEN a contradiction whose `fresh` is false and that is still changed after
  the queue refresh
- WHEN Check again is chosen
- THEN a quote of kind `contradiction` naming its `decision_key` is requested
  and the consent is shown, and no run is started

#### Scenario: A finding that no longer holds leaves To review

- GIVEN the person agreed to check a changed revision and the run retired its
  row
- WHEN the run ends
- THEN the app says the newer decision no longer replaces the earlier one, so
  it is no longer in To review, and closes the decision

#### Scenario: A check never counts a finding as made ready

- GIVEN an unjudged pair and a changed contradiction that becomes current
  during a check, from outside the app
- WHEN the check ends
- THEN the result counts only the pair as ready

#### Scenario: A changed pair offers only Check again and Decide later

- GIVEN a judged-same pair whose `fresh` is false
- WHEN it is opened
- THEN its options are Check again and Decide later, and no combine is offered

#### Scenario: Check again makes a pair decidable without a check

- GIVEN a changed pair that the queue refresh makes fresh
- WHEN Check again is chosen
- THEN the queue is refreshed, the decisions are listed again, and the pair is
  shown with Same thing, and no quote is requested

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
