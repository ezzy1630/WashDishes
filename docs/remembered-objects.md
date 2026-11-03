# Remembered objects

Not a dish and It's clean corrections immediately retain a linked contextual CLIP
ViT-B/32 exemplar, if one exists, in `remembered_objects`. No new model or nightly
training is needed. Historical records without linked features still receive the
correction, but cannot manufacture an exemplar. Clean remains a positive *dish
identity* training label; remembering it does not certify washing.

A new proposal with cosine similarity >= **0.940846**, in the same configured area
ID and dirty/sink role, is deferred with an explicit reason. Inventory retains a
pending candidate without opening a record. Ordinary settled verification retries
it, so removing the memory or undoing its correction permits a fresh observation
clock. Already accepted records are not retroactively rewritten. Different areas,
unknown areas, racks and cabinets cannot match. Near-identical clean cookware may
later become dirty; this bounded matching rule cannot certify that change.

The operator's Setup list shows source record, area, answer, reviewer and time.
`GET /api/remembered-objects` lists active metadata; authenticated, CSRF-protected
`DELETE /api/remembered-objects/{id}` forgets one. Neither endpoint is in the house
API. At most 64 stored exemplars remain; oldest entries are evicted. Correction
undo removes its own exemplar and restores a previous judgment's surviving
exemplar. Explicitly forgotten or evicted exemplars are never recreated by undo.
A later human decision supersedes an active correction. Can't tell and duplicate
answers create no exemplars. Undo remains operator-only and refuses later human
decisions. Visit corrections and undo are atomic across all members.

## Calibration

`scripts/calibrate_remembered.py --examples MANIFEST.json --vectors FEATURES.npy
--output RESULT.json` consumes private, cached features from the existing verifier.
The manifest records physical-object groups established by visual review, episode
IDs (shared across historical/replay views of the same episode), labels and actual
memory/proposal area IDs. Leave-one-out excludes every view of the target episode;
first/late views of one record cannot validate each other. Compatibility follows
the same area assignment as production. Missing compatible second instances are
reported as unmeasured, not successful matches. A round container is not silently
grouped with a green rectangular container.

On the Lane D / Lane G cohort, the largest incompatible or confirmed dirty match
was 0.9404186823. The next same-object match was 0.9412723860. The cutoff is their
midpoint rounded up to six decimals, leaving room for stored-feature rounding.
This gives zero vetoes of 43 positive images: all 17 confirmed historical dirty
records (first and late views), five recent replay target views and four older
positive targets. It catches 4/60 compatible directed cross-episode pairs, or
4/12 candidate images with any compatible other episode. These are reused audit
controls used to choose the cutoff, **not an independent generalization test**.
Canister/scale recurrence and clean-item cross-episode recall are unmeasured.

Coverage is limited: when only the earliest known instance of each recurring junk
object was marked in the chronological historical simulation, no later proposal
matched at this safe cutoff. The four human answers themselves remove four review
visits. This is an immediate conservative recurrence guard, not a reliable general
object recognizer. Full-footage recall and live Jetson performance remain separate
from this cached-photo evaluation. Private manifests, images, embeddings, counts,
and machine-specific reproduction scripts remain outside the repository.
