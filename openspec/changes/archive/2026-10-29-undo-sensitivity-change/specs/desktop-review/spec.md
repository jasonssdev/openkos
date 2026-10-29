# Desktop Review Specification (delta)

## MODIFIED Requirements

### Requirement: Who Can See A Page Is One Action Wherever A Page Is Shown

(Only the sentence on pages raised with the change changes: previously "When
other pages were raised with it, the confirmation MUST say so and MUST NOT offer
Undo, only Dismiss.")

When other pages were raised with it, the confirmation MUST say so. Where the
engine offers to put the whole change back (`previewUndoSensitivity` answers), the
confirmation MUST carry **Undo this change** and **Dismiss** and MUST NOT tell the
person to change each page on its own; **Undo this change** MUST apply the
previewed version through `applyUndoSensitivity`, retrying a busy apply for up to
five seconds, and MUST confirm which pages went back and which stayed as they
were. Where the engine does not offer it, the confirmation MUST keep the sentence
that says to change each page on its own and offer only **Dismiss**. A refused
undo MUST say, in the brief's words, that the pages changed since and nothing was
changed.

#### Scenario: The whole change is undone

- GIVEN a page marked Only on this Mac with 2 pages raised with it, and the
  engine offering the undo
- WHEN the person presses Undo this change
- THEN the version is applied and the screen says the page is back, and so are the
  pages that went back

#### Scenario: No record of the raised pages

- GIVEN a change the engine cannot undo as a whole
- WHEN the confirmation shows
- THEN it says to change each page on its own and offers only Dismiss
