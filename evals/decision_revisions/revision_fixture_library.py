"""The REAL fixture for the decision-revision-detector harness (#1014 piece
(a), sub-change 3, task T2): hand-written, English meeting notes from a
volunteer committee running a small community library -- deliberately NOT
AMI and not a product-design meeting, so tuning against this harness can
never contaminate the thesis's separate AMI evaluation (see this harness's
README).

`revision_fixtures.load_fixture()` keeps T1's tiny synthetic placeholder,
which `--self-test` pins exact numbers against; this module is what a live
run measures.

**Labels are BY CONSTRUCTION, not yet adjudicated.** The drafter wrote each
scenario and labelled it; every pair where a careful reader could
reasonably pick a different verdict carries `contested=True` and a one-line
note. The owner settles every contested pair (task T3) BEFORE any score is
trusted -- never after (project memory).

`expected_later_id` follows ONLY from the sources' dates below (via
`resolve_decision_date` + `pair_direction`), never from the narrative:
the harness's own `fixture_integrity` check recomputes it and fails on any
disagreement.

Hard-case tags (`LabelledPair.hard_case`):

- `chain`: an A->B->C thread (reverse, then refine).
- `reversal-low-overlap`: a reversal sharing almost no words with what it
  reverses.
- `reversal-reads-as-refine`: a reversal with narrowing/keeping language
  that could be misread as a refinement.
- `partial-refine-reads-as-reversal`: a refinement that changes part of the
  choice and could be misread as a reversal.
- `refine-reads-as-reaffirm`: a refinement that mostly restates the choice.
- `reaffirm-different-words`: the same choice restated in other words.
- `paraphrase`: the two subjects are paraphrases with low lexical overlap,
  so the candidate stage may never propose the pair.
- `same-subject-unrelated`: same subject area, but neither choice bears on
  the other.
- `shared-cue-unrelated`: different subjects that share a lure word
  ("free", "no longer", "reading", "charge").
- `shared-source`: near-duplicates from ONE meeting -- the candidate stage
  must never pair them.
- `equal-date`, `undated`, `multi-date`: the direction rule cannot order
  the pair (`pair_direction` reason `equal`, `missing`, `multiple`).
"""

from __future__ import annotations

from datetime import date

from revision_fixtures import (
    DecisionDoc,
    Fixture,
    LabelledPair,
    RevisionExpectation,
    SourceDoc,
)

# ---------------------------------------------------------------------------
# Sources: the committee's meetings, one volunteer huddle on the same day as
# a committee meeting, and two undated sources.
# ---------------------------------------------------------------------------

_JAN = SourceDoc("sources/committee-2026-01-14", date(2026, 1, 14))
_FEB = SourceDoc("sources/committee-2026-02-11", date(2026, 2, 11))
_MAR = SourceDoc("sources/committee-2026-03-11", date(2026, 3, 11))
_MAR_HUDDLE = SourceDoc("sources/volunteer-huddle-2026-03-11", date(2026, 3, 11))
_APR = SourceDoc("sources/committee-2026-04-08", date(2026, 4, 8))
_MAY = SourceDoc("sources/committee-2026-05-13", date(2026, 5, 13))
_EMAIL = SourceDoc("sources/email-thread-it-volunteers", None)
_WHITEBOARD = SourceDoc("sources/front-desk-whiteboard-photo", None)

_SOURCES: tuple[SourceDoc, ...] = (
    _JAN,
    _FEB,
    _MAR,
    _MAR_HUDDLE,
    _APR,
    _MAY,
    _EMAIL,
    _WHITEBOARD,
)


def _d(concept_slug: str, title: str, body: str, *sources: SourceDoc) -> DecisionDoc:
    return DecisionDoc(
        f"decisions/{concept_slug}",
        title,
        body,
        tuple(source.source_id for source in sources),
    )


_DECISIONS: tuple[DecisionDoc, ...] = (
    # -- Saturday opening: the A -> B -> C chain ----------------------------
    _d(
        "saturday-opening-hours",
        "Saturday Opening Hours",
        "Several regulars asked for weekend access. The library will open on "
        "Saturdays from 10:00 to 14:00. Two volunteers will cover each "
        "Saturday.",
        _JAN,
    ),
    _d(
        "saturday-closure",
        "Saturday Closure",
        "Saturday footfall has averaged six visitors since January. The "
        "library will no longer open on Saturdays from April onward. The "
        "volunteer hours saved move to the after-school slot.",
        _MAR,
    ),
    _d(
        "first-saturday-book-swap-opening",
        "First-Saturday Book Swap Opening",
        "Saturdays remain closed in general. The library will open on the "
        "first Saturday of each month from 10:00 to 12:00 for the book swap.",
        _APR,
    ),
    # -- Fines -------------------------------------------------------------
    _d(
        "overdue-fines",
        "Overdue Fines",
        "The treasurer proposed simple fines to encourage returns. Overdue "
        "books will be charged 25 cents per day, capped at five dollars per "
        "item.",
        _JAN,
    ),
    _d(
        "no-charges-for-late-returns",
        "No Charges for Late Returns",
        "Fines were keeping families away, and collecting them cost more "
        "volunteer time than they raised. Borrowers will no longer pay "
        "anything when they bring materials back late.",
        _MAR,
    ),
    _d(
        "lost-item-replacement-billing",
        "Lost Item Replacement Billing",
        "An item not returned within 60 days will be treated as lost and "
        "billed at its replacement cost. Late returns under 60 days still "
        "cost nothing.",
        _APR,
    ),
    _d(
        "fine-free-policy-review",
        "Fine-Free Policy Review",
        "Returns went up after fines were dropped, according to the "
        "circulation report. The committee kept the library fine-free for "
        "late returns.",
        _MAY,
    ),
    # -- Volunteer desk shifts ---------------------------------------------
    _d(
        "volunteer-desk-shift-length",
        "Volunteer Desk Shift Length",
        "Volunteer desk shifts will be three hours long. The rota will be "
        "drawn up in blocks of three hours from March.",
        _FEB,
    ),
    _d(
        "desk-shift-length-review",
        "Desk Shift Length Review",
        "Some volunteers asked for shorter shifts, and two-hour shifts were "
        "discussed. Desk shifts remain at three hours for now.",
        _MAR,
    ),
    _d(
        "evening-shift-length",
        "Evening Shift Length",
        "Weekday evening desk shifts will be two hours instead of three, "
        "because the library closes at 20:00. Daytime desk shifts stay at "
        "three hours.",
        _APR,
    ),
    _d(
        "new-volunteer-training",
        "New Volunteer Training",
        "Every new volunteer will complete a one-hour desk training before "
        "their first shift.",
        _MAY,
    ),
    # -- Donations ---------------------------------------------------------
    _d(
        "book-donation-acceptance",
        "Book Donation Acceptance",
        "Donations are overwhelming the storage room. We will accept donated "
        "books only if they were published in the last ten years and are in "
        "good condition.",
        _JAN,
    ),
    _d(
        "donation-criteria",
        "Donation Criteria",
        "The front desk asked what to tell donors. Donated titles older than "
        "a decade or in poor shape will continue to be turned away.",
        _MAY,
    ),
    _d(
        "unshelved-donations",
        "Unshelved Donations",
        "Donated books we cannot shelve will be sold at the book sale rather "
        "than recycled.",
        _APR,
    ),
    # -- Reading room ------------------------------------------------------
    _d(
        "reading-room-armchairs",
        "Reading Room Armchairs",
        "The reading room will get six new armchairs funded by the spring appeal.",
        _FEB,
    ),
    _d(
        "reading-room-quiet-mornings",
        "Reading Room Quiet Mornings",
        "Students asked for somewhere to study. The reading room will be a "
        "silent space every weekday before noon.",
        _APR,
    ),
    # -- Newsletter --------------------------------------------------------
    _d(
        "volunteer-newsletter-frequency",
        "Volunteer Newsletter Frequency",
        "The volunteer newsletter will go out once a month by email.",
        _JAN,
    ),
    _d(
        "newsletter-send-day",
        "Newsletter Send Day",
        "The newsletter stays monthly and will now always be sent on the "
        "first Monday of the month.",
        _MAR,
    ),
    _d(
        "newsletter-schedule",
        "Newsletter Schedule",
        "No change to the newsletter was proposed. The newsletter continues "
        "to go out monthly on the first Monday.",
        _APR,
    ),
    # -- Children's programmes ---------------------------------------------
    _d(
        "childrens-story-time",
        "Children's Story Time",
        "Story time for children will run on Wednesday mornings at 10:30.",
        _FEB,
    ),
    _d(
        "story-time-snacks",
        "Story Time Snacks",
        "Because of allergy concerns, story time will no longer serve juice or snacks.",
        _MAR,
    ),
    _d(
        "toddler-read-aloud-session",
        "Toddler Read-Aloud Session",
        "The nursery next door now naps on Wednesday mornings. The read-aloud "
        "session for toddlers moves to Thursday afternoons at 15:00.",
        _MAY,
    ),
    _d(
        "summer-reading-challenge-ages",
        "Summer Reading Challenge Ages",
        "The summer reading challenge will be open to children aged 5 to 12.",
        _JAN,
    ),
    _d(
        "summer-reading-challenge-for-teens",
        "Summer Reading Challenge for Teens",
        "The county now runs a challenge for younger children. This year the "
        "summer reading challenge will be for teenagers aged 13 to 17 only, "
        "and the children's version is dropped.",
        _APR,
    ),
    # -- Membership --------------------------------------------------------
    _d(
        "library-card-fee",
        "Library Card Fee",
        "New members will pay two dollars for a library card, to cover the card stock.",
        _JAN,
    ),
    _d(
        "joining-cost",
        "Joining Cost",
        "A grant from the town council now pays for the cards. Signing up to "
        "borrow is free for everyone from May onward.",
        _APR,
    ),
    # -- Spring book sale: near-duplicates in ONE meeting ------------------
    _d(
        "spring-book-sale-date",
        "Spring Book Sale Date",
        "The spring book sale will be held on 25 April in the community hall.",
        _MAR,
    ),
    _d(
        "spring-book-sale",
        "Spring Book Sale",
        "The spring book sale is set for 25 April at the community hall. "
        "Doors open at 9:00.",
        _MAR,
    ),
    _d(
        "book-sale-volunteers",
        "Book Sale Volunteers",
        "Four volunteers will staff the spring book sale tables, in two shifts.",
        _MAR_HUDDLE,
    ),
    # -- Returns -----------------------------------------------------------
    _d(
        "returns-drop-box",
        "Returns Drop Box",
        "Returns will be accepted through the outside drop box at all times.",
        _MAR,
    ),
    _d(
        "drop-box-overnight-lock",
        "Drop Box Overnight Lock",
        "After last week's water damage, the outside drop box will be locked "
        "from 20:00 to 08:00.",
        _MAR_HUDDLE,
    ),
    _d(
        "returns-reshelving",
        "Returns Reshelving",
        "Returned books will be reshelved within one working day.",
        _MAY,
    ),
    # -- Guest Wi-Fi and printing (undated sides) --------------------------
    _d(
        "guest-wifi-password-rotation",
        "Guest Wi-Fi Password Rotation",
        "The guest Wi-Fi password will be changed every month and posted at the desk.",
        _EMAIL,
    ),
    _d(
        "guest-wifi-access",
        "Guest Wi-Fi Access",
        "Visitors kept asking for the password. Guest Wi-Fi will need no "
        "password at all.",
        _FEB,
    ),
    _d(
        "open-guest-network",
        "Open Guest Network",
        "Visitors can keep joining the guest network without entering any code.",
        _MAY,
    ),
    _d(
        "printing-charges",
        "Printing Charges",
        "Printing costs ten cents per page, black and white only.",
        _WHITEBOARD,
    ),
    _d(
        "colour-printing",
        "Colour Printing",
        "Colour printing will be offered at fifty cents per page. Black and "
        "white printing stays at ten cents per page.",
        _MAY,
    ),
    # -- Meeting room (multi-date side) ------------------------------------
    _d(
        "meeting-room-booking-terms",
        "Meeting Room Booking Terms",
        "Discussed in February and finalized in March. Community groups may "
        "book the meeting room for free, up to two hours per week.",
        _FEB,
        _MAR,
    ),
    _d(
        "meeting-room-booking-charge",
        "Meeting Room Booking Charge",
        "Heating costs have doubled. Community groups will now pay ten "
        "dollars per booking of the meeting room.",
        _MAY,
    ),
    _d(
        "meeting-room-projector",
        "Meeting Room Projector",
        "The meeting room projector will be replaced with a wall-mounted screen.",
        _APR,
    ),
    _d(
        "book-club-venue",
        "Book Club Venue",
        "The monthly book club will meet in the library's meeting room.",
        _FEB,
    ),
    _d(
        "book-club-at-the-corner-cafe",
        "Book Club at the Corner Cafe",
        "The monthly book club will keep its second-Tuesday schedule but meet "
        "at the Corner Cafe instead of the library.",
        _MAY,
    ),
)


def _p(
    a: str,
    b: str,
    verdict: RevisionExpectation,
    later: str | None,
    *,
    note: str,
    hard_case: str | None = None,
    contested: bool = False,
) -> LabelledPair:
    later_id = None if later is None else f"decisions/{later}"
    return LabelledPair(
        (f"decisions/{a}", f"decisions/{b}"),
        verdict,
        later_id,
        contested=contested,
        note=note,
        hard_case=hard_case,
    )


_PAIRS: tuple[LabelledPair, ...] = (
    # == REVERSES =========================================================
    _p(
        "saturday-opening-hours",
        "saturday-closure",
        "REVERSES",
        "saturday-closure",
        note="Saturday opening is withdrawn outright.",
        hard_case="chain",
    ),
    _p(
        "saturday-opening-hours",
        "first-saturday-book-swap-opening",
        "REVERSES",
        "first-saturday-book-swap-opening",
        contested=True,
        note="Every-Saturday opening became one Saturday a month, 10-12; "
        "could be read as REFINES (Saturday hours narrowed) rather than "
        "overturned.",
        hard_case="chain",
    ),
    _p(
        "overdue-fines",
        "no-charges-for-late-returns",
        "REVERSES",
        "no-charges-for-late-returns",
        note="Per-day fines replaced by no charge at all, in different words.",
        hard_case="reversal-low-overlap",
    ),
    _p(
        "overdue-fines",
        "fine-free-policy-review",
        "REVERSES",
        "fine-free-policy-review",
        contested=True,
        note="The later side keeps the library fine-free, which overturns "
        "the per-day fine; but it restates an earlier reversal rather than "
        "making one, so a reader may not call it REVERSES of this Decision.",
    ),
    _p(
        "summer-reading-challenge-ages",
        "summer-reading-challenge-for-teens",
        "REVERSES",
        "summer-reading-challenge-for-teens",
        contested=True,
        note="Audience replaced (5-12 dropped, 13-17 only); the word 'only' "
        "and 'this year' could make it read as REFINES.",
        hard_case="reversal-reads-as-refine",
    ),
    _p(
        "book-club-venue",
        "book-club-at-the-corner-cafe",
        "REVERSES",
        "book-club-at-the-corner-cafe",
        contested=True,
        note="Venue replaced; 'will keep its schedule' could make it read "
        "as REFINES of the book club arrangement.",
        hard_case="reversal-reads-as-refine",
    ),
    _p(
        "childrens-story-time",
        "toddler-read-aloud-session",
        "REVERSES",
        "toddler-read-aloud-session",
        contested=True,
        note="Drafter treats the toddler read-aloud as the same story time, "
        "moved to another day and time; a reader may call it a different "
        "event (UNRELATED) or a reschedule (REFINES).",
        hard_case="paraphrase",
    ),
    _p(
        "library-card-fee",
        "joining-cost",
        "REVERSES",
        "joining-cost",
        note="Two-dollar card fee replaced by free sign-up; subjects are paraphrases.",
        hard_case="paraphrase",
    ),
    _p(
        "guest-wifi-password-rotation",
        "guest-wifi-access",
        "REVERSES",
        None,
        note="Rotating password vs no password; the email thread is undated, "
        "so order is unknown and the verdict is symmetric.",
        hard_case="undated",
    ),
    _p(
        "guest-wifi-password-rotation",
        "open-guest-network",
        "REVERSES",
        None,
        note="Rotating password vs joining without any code; one side undated.",
        hard_case="undated",
    ),
    _p(
        "meeting-room-booking-terms",
        "meeting-room-booking-charge",
        "REVERSES",
        None,
        contested=True,
        note="Free booking replaced by a ten-dollar charge; could be REFINES "
        "(booking kept, a fee added). The terms Decision cites two meetings "
        "with different dates, so order is unknown.",
        hard_case="multi-date",
    ),
    # == REFINES ==========================================================
    _p(
        "saturday-closure",
        "first-saturday-book-swap-opening",
        "REFINES",
        "first-saturday-book-swap-opening",
        contested=True,
        note="Closure kept with a monthly exception; could be read as a "
        "partial REVERSES of the closure.",
        hard_case="chain",
    ),
    _p(
        "no-charges-for-late-returns",
        "lost-item-replacement-billing",
        "REFINES",
        "lost-item-replacement-billing",
        contested=True,
        note="Late returns stay free, conditioned by a 60-day lost-item "
        "charge; charging anything could be read as REVERSES.",
        hard_case="partial-refine-reads-as-reversal",
    ),
    _p(
        "volunteer-desk-shift-length",
        "evening-shift-length",
        "REFINES",
        "evening-shift-length",
        contested=True,
        note="Three hours kept for daytime, evenings cut to two; the evening "
        "change could be read as a partial REVERSES.",
        hard_case="partial-refine-reads-as-reversal",
    ),
    _p(
        "desk-shift-length-review",
        "evening-shift-length",
        "REFINES",
        "evening-shift-length",
        contested=True,
        note="Same as the pair above, against the reaffirmation of three "
        "hours; the evening change could be read as a partial REVERSES.",
        hard_case="partial-refine-reads-as-reversal",
    ),
    _p(
        "volunteer-newsletter-frequency",
        "newsletter-send-day",
        "REFINES",
        "newsletter-send-day",
        contested=True,
        note="Monthly kept, a fixed send day added; mostly a restatement, so "
        "REAFFIRMS is a reasonable reading.",
        hard_case="refine-reads-as-reaffirm",
    ),
    _p(
        "volunteer-newsletter-frequency",
        "newsletter-schedule",
        "REFINES",
        "newsletter-schedule",
        contested=True,
        note="Relative to plain 'monthly', the first-Monday detail narrows "
        "it; 'No change ... was proposed' pushes toward REAFFIRMS.",
        hard_case="refine-reads-as-reaffirm",
    ),
    _p(
        "returns-drop-box",
        "drop-box-overnight-lock",
        "REFINES",
        None,
        contested=True,
        note="Drop box kept but locked overnight, narrowing 'at all times'; "
        "could be read as REVERSES of 'at all times'. Same day, so no order.",
        hard_case="equal-date",
    ),
    _p(
        "printing-charges",
        "colour-printing",
        "REFINES",
        None,
        contested=True,
        note="Black and white stays at ten cents; colour is added. Colour "
        "printing may be read as a new subject (UNRELATED). The whiteboard "
        "note is undated.",
        hard_case="undated",
    ),
    # == REAFFIRMS ========================================================
    _p(
        "volunteer-desk-shift-length",
        "desk-shift-length-review",
        "REAFFIRMS",
        "desk-shift-length-review",
        note="Three hours kept; the discussed two-hour option is a lure, not a change.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "book-donation-acceptance",
        "donation-criteria",
        "REAFFIRMS",
        "donation-criteria",
        note="Same ten-year / good-condition rule, restated as what is turned away.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "guest-wifi-access",
        "open-guest-network",
        "REAFFIRMS",
        "open-guest-network",
        note="No password vs no code: the same choice in other words.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "no-charges-for-late-returns",
        "fine-free-policy-review",
        "REAFFIRMS",
        "fine-free-policy-review",
        note="Fine-free kept after review; 'fines were dropped' is a "
        "backward reference, not a new change.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "newsletter-send-day",
        "newsletter-schedule",
        "REAFFIRMS",
        "newsletter-schedule",
        note="Monthly on the first Monday, restated.",
    ),
    # == UNRELATED ========================================================
    _p(
        "reading-room-armchairs",
        "reading-room-quiet-mornings",
        "UNRELATED",
        "reading-room-quiet-mornings",
        note="Same room; furniture vs quiet hours.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "spring-book-sale-date",
        "spring-book-sale",
        "UNRELATED",
        None,
        contested=True,
        note="Near-duplicates extracted from ONE meeting: not a change over "
        "time, labelled UNRELATED so the correct candidate-stage exclusion "
        "is not scored as a miss; a text-only judge would say REAFFIRMS.",
        hard_case="shared-source",
    ),
    _p(
        "spring-book-sale-date",
        "book-sale-volunteers",
        "UNRELATED",
        None,
        note="Same sale; date/venue vs staffing. Same day, so no order.",
        hard_case="equal-date",
    ),
    _p(
        "saturday-closure",
        "drop-box-overnight-lock",
        "UNRELATED",
        None,
        note="Both restrict access (a closure, a lock) but on different "
        "things. Same day, so no order.",
        hard_case="equal-date",
    ),
    _p(
        "book-donation-acceptance",
        "unshelved-donations",
        "UNRELATED",
        "unshelved-donations",
        contested=True,
        note="Which donations are accepted vs what happens to ones not "
        "shelved; could be read as REFINES of the donation policy.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "volunteer-desk-shift-length",
        "new-volunteer-training",
        "UNRELATED",
        "new-volunteer-training",
        note="Shift length vs training before a first shift.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "childrens-story-time",
        "story-time-snacks",
        "UNRELATED",
        "story-time-snacks",
        note="Schedule vs snacks; 'no longer' is a reversal lure on a "
        "different choice.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "story-time-snacks",
        "toddler-read-aloud-session",
        "UNRELATED",
        "toddler-read-aloud-session",
        note="Snacks vs session day and time.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "returns-drop-box",
        "returns-reshelving",
        "UNRELATED",
        "returns-reshelving",
        note="How returns come in vs how fast they are reshelved.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "drop-box-overnight-lock",
        "returns-reshelving",
        "UNRELATED",
        "returns-reshelving",
        note="Drop-box hours vs reshelving speed.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "meeting-room-booking-terms",
        "meeting-room-projector",
        "UNRELATED",
        None,
        note="Booking terms vs equipment; the terms side has two dates.",
        hard_case="multi-date",
    ),
    _p(
        "meeting-room-booking-charge",
        "meeting-room-projector",
        "UNRELATED",
        "meeting-room-booking-charge",
        note="Booking charge vs equipment.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "book-club-venue",
        "meeting-room-projector",
        "UNRELATED",
        "meeting-room-projector",
        note="The book club meets in the room; the projector choice does not "
        "bear on that.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "reading-room-armchairs",
        "meeting-room-projector",
        "UNRELATED",
        "meeting-room-projector",
        note="Two furnishing purchases for different rooms.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "colour-printing",
        "meeting-room-projector",
        "UNRELATED",
        "colour-printing",
        note="Two equipment/service choices with no bearing on each other.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "no-charges-for-late-returns",
        "joining-cost",
        "UNRELATED",
        "joining-cost",
        note="Both make something free (late returns, joining); different subjects.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "library-card-fee",
        "lost-item-replacement-billing",
        "UNRELATED",
        "lost-item-replacement-billing",
        note="Both are charges to borrowers; different subjects.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "reading-room-quiet-mornings",
        "toddler-read-aloud-session",
        "UNRELATED",
        "toddler-read-aloud-session",
        note="'Reading' and a weekday schedule in both; different subjects.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "book-donation-acceptance",
        "spring-book-sale-date",
        "UNRELATED",
        "spring-book-sale-date",
        note="Donation acceptance vs the sale date.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "childrens-story-time",
        "summer-reading-challenge-for-teens",
        "UNRELATED",
        "summer-reading-challenge-for-teens",
        note="Two children's/youth programmes; 'the children's version is "
        "dropped' is a reversal lure aimed at a different programme.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "toddler-read-aloud-session",
        "summer-reading-challenge-for-teens",
        "UNRELATED",
        "toddler-read-aloud-session",
        note="Two youth programmes with different audiences and choices.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "book-sale-volunteers",
        "new-volunteer-training",
        "UNRELATED",
        "new-volunteer-training",
        note="Volunteers in both; staffing one event vs onboarding training.",
        hard_case="same-subject-unrelated",
    ),
)


def load_library_fixture() -> Fixture:
    """The real T2 fixture: a community-library volunteer committee's
    meeting notes. Labels by construction, pending owner adjudication of
    every `contested` pair (T3)."""
    return Fixture(sources=_SOURCES, decisions=_DECISIONS, pairs=_PAIRS)
