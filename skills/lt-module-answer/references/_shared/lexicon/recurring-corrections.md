---
name: recurring-corrections
description: Corrections made to drafts, aggregated across runs by run review
review_by: 2027-10-24
evidence: [docs/design-notes.md#state-split]
status: empty-by-construction
---

# Recurring corrections

This entry ships empty, and that is its correct state until run review has evidence.

The diff between the user's edit of an exported page and the draft is the signal that drives improvement, and the only evidence permitted to drive a skill edit. A fresh install has no such diff.

Writing plausible entries here would be worse than leaving it blank. A fabricated correction is indistinguishable in this file from a real one, and it would then justify a skill edit that no evidence supports.

## What fills it

The loop, in order:

1. The user reviews an exported page and edits it by hand.
2. A re-export refuses to overwrite that edit and keeps the unified diff, because discarding the diff discards the loop.
3. Run review (`improve.py ingest`, in the lt-suite-setup skill) records one classified entry per change into the run record: `factual`, `provenance`, `style`, `scope` or `diagram`, plus whether the change shows the run was steered wrong.
4. `improve.py propose` aggregates across runs and proposes a concrete diff naming the runs that justify it, once at least two runs corroborate. One run is an anecdote.
5. The user approves, the edit is applied by hand, the evaluations are re-run, and the before-and-after numbers decide. An improvement that moves no number is rejected, however sensible it sounds.

A correction becomes an entry when it has been seen in two or more runs and the proposal it justified was accepted. Proposals are written to the user's data folder (`<data>/lexicon/`), never into the shipped lexicon. Until then a correction lives in the run records, where it can be counted but cannot quietly become received wisdom.
