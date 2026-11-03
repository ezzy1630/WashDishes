# Object hints in the naming queue

This is a review aid, not an admission veto. It does not settle ownership, dirty
state, duplicates, washing or points. A weak visit remains available to review and
name. Only visits whose **every canonical member** has an object hint are demoted;
an unknown or real-dish member keeps the trip visible. A retained still with a
positive tight-crop score can earn fair, protecting dishes the kitchen proof
threshold previously left weak.

The intake verifier already embeds a tight crop for its text comparison. We now
save that vector separately from its contextual training vector in
`dish_samples.tight_embedding`. No second image pass is added. The house API reads
saved vectors and an explicitly adopted small linear model; it never runs inference, downloads models
or changes the camera. Old rows without tight features keep their previous rating
unless they match an explicit same-area Not a dish correction. Clean-object memory
does not teach the new review demotion: a clean pan can be dirty on its next trip.
Explicit corrections continue teaching the existing nightly kitchen probe and
remembered-object memory. Undo/removal withdraws that memory from review.

`review-probe.json` is an **unadopted candidate** containing development-trained
coefficients, not images or names. Only a model explicitly installed at
`models/dish-review-probe.json` is active; the candidate is not loaded automatically. Missing or
invalid vectors/models abstain. A valid operator model with zero weights/bias and thresholds 0 and 1
can disable the new learned hints; the original evidence rating and explicit
object-memory behavior remain. Model changes require fresh evaluation.

## Reproduction

`tests/fixtures/review-objects-truth.jsonl` is an object-only, photo-reviewed set of
459 records: all 455 live IDs 1230–1684 in the Oct 26 deadline week through the
snapshot, plus report-named older controls 897, 901, 917, 945. It contains 32 dirty,
54 non-dish, seven duplicate and 366 uncertain labels. A clean-looking pan stays
uncertain because endpoint photos do not prove washing. Photo hashes bind labels
to the inspected first/latest views; boxes refer to those captures. Photos remain
private, outside Git. No person descriptions or attribution labels are included.

The SHA-256 integer of the decimal record ID, modulo two, fixes development (0)
and holdout (1). Both endpoints of a record stay together. Repeated physical
objects can occur in both halves: this measures recurring views, not unseen-object
or independent-episode recall. Training protects every uncertain/duplicate view
as positive dish identity; that is not a dirty-state verdict.

Run on a **local snapshot**, with caches and outputs in private scratch:

```sh
python scripts/review_objects.py features --data SNAPSHOT --truth tests/fixtures/review-objects-truth.jsonl --out SCRATCH/features.npz
python scripts/review_objects.py train --truth tests/fixtures/review-objects-truth.jsonl --features SCRATCH/features.npz --out SCRATCH/probe.json
python scripts/review_objects.py measure --data SNAPSHOT --truth tests/fixtures/review-objects-truth.jsonl --features SCRATCH/features.npz --out SCRATCH/measurement --candidate-model wash_dishes/review-probe.json --history-start 2026-10-26
```

Training writes a candidate, never adopts it. Measurement backs up the input
SQLite database through its read-only connection and modifies only the new output
copy. It backfills that copy's latest linked sample using the first source-photo
features. It never labels/dismisses a historical record. The history-start option
projects a week before the live league's start in this copy only.

## Limits of this calibration

With the candidate explicitly activated on the isolated Nov 3 snapshot, holdout dirty records in weak: 2 before, **0 after**, with
**0 new demotions and 0 admission vetoes**. Holdout non-dishes in strong/fair:
**11/28 before, 10/28 after** (two leave, one enters). None of the 178 holdout
uncertain records is newly demoted. These are record-level measurements, not a
promise of zero errors on future dishes.

The weekly default queue **grows**: Oct 26 retrospective projection 82 → 106;
current Nov 2 week 33 → 37. Preserving the known dirty dishes also surfaces many
uncertain visits. This calibration does **not** meet the broader workload-reduction
goal, so the candidate stays unadopted. With no adopted model, the source keeps
the previous learned ranking and only adds explicit object-memory demotion. Historical rows need a separately authorized backfill to benefit; deploying
source alone does not recreate missing tight features. No backfill was applied on
the Jetson.

Contextual-only models, lower regularization and a zero-shot promotion guard were
explored and rejected. Development labels alone trained all models; thresholds
and regularization were examined on development folds, but multiple experimental
holdout results were inspected. This is an exploratory record split, not an
untouched final external validation. A fresh later-time cohort is required before
claiming a validated admission policy. The full private report and receipts are
in `/Volumes/Neural/dishwatch-scratch/houseleague/junk/REPORT.md`.
