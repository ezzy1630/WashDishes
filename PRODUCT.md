# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vanilla JavaScript ES modules and plain CSS served as static files by the FastAPI app (`wash_dishes/static`). No build step. Chosen with the user on 2026-10-09: the Jetson only serves files and the laptop browser renders, so a framework would add tooling without changing Jetson load. Strict CSP: `script-src 'self'`, no external fonts, CDNs, or inline scripts.

## Users

The household operator and housemates in one shared kitchen. Primary use is a laptop or desktop browser reached over Tailscale, in sessions of a few minutes: check what is in the kitchen right now, see who owes dishes, and answer "whose was it?" on new items.

## Product Purpose

Dish Watch records dishes left out in a shared kitchen, starts a cleanup deadline (default 120 minutes), keeps photo and clip evidence, and maintains a corrected ledger of who left what and whether they washed it. Success is housemates being accountable on evidence they accept.

## Positioning

A camera over the counters with detection running on a Jetson Orin Nano in the kitchen. Footage, faces, and the ledger stay in the house. The camera proposes; a person confirms, until the camera's suggestions are measured accurate enough (30+ checks at 90%+). Then it names owners itself, with spot checks, and every name can be disputed.

## Operating Context

- The primary Photos screen leads with Out now: one recent analysed camera picture, cropped to the counters, with numbered detector boxes matching a list of every active dish record (dirty or sink areas). Each row shows when tracking started, the next photo time, and which photos are not attached yet. A dish without an owner, handling view or earlier photos still appears. The picture is labelled with its time and as detector suggestions, never as a verified count; without a fresh analysed frame it is omitted, and the list stands alone. Ready to share beside it holds only complete four-view sequences with a matching view at least two hours after the unattended view, each with Download. Incomplete records never become placeholder collages. Live kitchen opens the separate camera and investigation screen. Archive preserves older records outside current counts.
- Opening a photo shows the image and Download. An incomplete record shows one saved view, cropped around its dish box and labelled as saved rather than live, plus a four-line list of which views are attached; a stage that is not attached is not described as missing footage. Something wrong? expands a small correction form; Original photos & video preserves the full-scene sources. Detailed record retains the advanced atomic review workflow. Naming the visible person alone does not assign responsibility.
- A share image (1200px wide JPEG, Mona Sans with a system-font fallback) uses four genuine frames cropped to the same 4:3 region around the dish boxes, with extra room above for the handling action, so they remain readable on a phone. A header names the dish, place and date, then "Left out <time> · same spot <duration> later". Short stage/time labels sit on the photos with dish boxes; one footer line says activity between photos is not shown. Blue marks the earlier comparison position; it does not claim absence. The displayed duration is measured between the unattended and later frames, and sampled views do not establish uninterrupted non-washing.
- Setup covers the camera connection, named monitoring areas (counters, sink, racks, cabinets) drawn on a snapshot, empty-area references for sink fullness, deadline, retention, and detection.
- Training: users correct detector boxes on saved frames to fine-tune a model for this kitchen.
- Demo mode runs with fictional housemates and synthetic evidence; it must always be labeled as demo.

## Capabilities and Constraints

- Statuses: pending, overdue (past deadline), review, disputed, resolved (washing confirmed or removed with washing unknown), cleared (taken away before the deadline), dismissed. Each detected dish in a dirty or sink area gets its own photo record, including startup inventory. Startup records retain unknown original departure and ownership; their two-hour follow-up starts at observation. Continued localized handling before the follow-up restarts that clock from the next unattended view. A fresh matching detection is required for the later photo, including while the surrounding area is busy. Earlier genuine captures are retained or recovered where available; missing history stays explicit. Rack or storage arrivals close the dirty episode without claiming washing. Missing-after-gap records are not currently-out dishes.
- A passed deadline without continuous observation is "unverified" and excluded from overdue statistics. Disputed and dismissed items are excluded from named statistics.
- Area roles: dirty (counters/landing areas), sink, clean (racks), storage (cabinets).
- Analysis rate depends on detector load on the Jetson; smooth video comes from a go2rtc relay as fragmented MP4. Frame-by-frame JPEG fallback exists.
- The Jetson also runs inference; the dashboard must keep polling and payloads modest.

## Evidence on Hand

The Jetson is the live source. Authorized isolated snapshots under `.data/` may contain private real household records and media; they are not demo or live validation. Demo data in `.data/demo` is synthetic. Never include private snapshots, faces, household labels or credentials in a source release.

## Product Principles

1. Show the evidence, then ask. Every attribution traces to a photo, clip, or a person's confirmation.
2. A finished result first. Show complete photos and a clear Download action; keep collection progress and advanced review secondary. Corrections remain atomic, with explicit unknowns and protection against stale evidence.
3. Honest uncertainty. Unknown, unverified, and backlog states are shown as such, never rounded into guilt or success.
4. Local and private by default.
