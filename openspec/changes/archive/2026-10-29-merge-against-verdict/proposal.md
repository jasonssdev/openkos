# Proposal: merge-against-verdict

## Intent

Closes #1699. The decision surface offered "Same thing" (merge) only when the
judge said SAME. For UNCERTAIN, DIFFERENT and pending evaluation it was
withheld, so a person who knew two pages were the same, with a judge that
"couldn't tell", had no way to combine them in the app. The judge is advisory;
the person is the authority.

Success: a pair of one type judged UNCERTAIN or DIFFERENT lists the merge
action, the screen says the model's doubt beside it, and the combine applies
and undoes like any other.

## Scope

### In Scope

1. `_identity_actions` lists merge for a judged-UNCERTAIN or judged-DIFFERENT
   pair that passes the cross-type check.
2. The review screen's line above the question adds that the person can still
   combine and split again, only when the engine offers merge on a current
   decision.

### Out of Scope

- Pending evaluation stays withheld (no verdict to show).
- Groups of more than two and cross-type pairs keep their guards and stay
  withheld against a non-SAME verdict, merge-group included.
- No API schema change: the surface is the existing `actions` and `withheld`.
