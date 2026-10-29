# Proposal: cluster-near-matches

## Intent

Refs #1701 (first slice; attach-at-ingest stays open there). A subject that recurs
across a meeting series left one page paired separately with five or six
near-duplicates, so the person answered the same underlying question many times
(70 open items after nine transcripts). Ask one group-shaped question per
subject instead of one per pair.

Success: the near-match (LOW tier) pairs of one subject form one group candidate
per connected component, asked through the existing group walk, and every pair
that was asked before is still decidable.

## Scope

### In Scope

1. `find_candidates_report` folds each type's LOW pairs into connected components
   and returns one LOW group per component of 3 to 8 members.
2. A component of two stays a pair. A component of more than 8, or one holding a
   pair already asked as a HIGH or ACRONYM candidate, stays as its pairs.
3. A LOW cluster judged UNCERTAIN or DIFFERENT lists `merge_group` (the pair it
   replaces listed `merge` on any verdict, #1699); an exact-title group stays
   SAME-only; a cluster not yet judged withholds it.
4. The review screen says the doubt and that the person can still combine.

### Out of Scope

- No new similarity threshold, no change to which pairs are candidates, no
  change to merge semantics or guards. Cross-type pairs never cluster.
- Fuzzy attach-at-ingest (needs a pre-registered measurement; #1701 stays open).
- Honouring a keep-distinct ruling recorded for a former pair inside a newly
  formed cluster: rulings are keyed on the exact member set, so such a cluster is
  asked once as a whole.

## Approach

Clustering is a pure post-step of the existing per-type pass over the LOW pairs it
already produces (`_cluster_low_pairs`). A cluster's trigger is its weakest pair
score. Members may chain (A~B, B~C), which is why the group walk lets the person
mark a subset. The bound (`_MAX_CLUSTER_MEMBERS = 8`) keeps one chain of loosely
similar titles from forming a question too large to read; above it the questions
are exactly the pairs of today.
