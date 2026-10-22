# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vanilla JavaScript ES modules and plain CSS served as static files by the FastAPI app (`wash_dishes/static`). No build step. Chosen with the user on 2026-10-09: the Jetson only serves files and the laptop browser renders, so a framework would add tooling without changing Jetson load. Strict CSP: `script-src 'self'`, no external fonts, CDNs, or inline scripts.

## Users

The household operator and housemates in one shared kitchen. Primary use is the operator's iPhone over Tailscale, in sessions of under a minute: open the dashboard, grab the proof picture of a dish left out and send it to the house group chat, tapping who put it down when they know. A laptop browser is used for setup and the live camera.

## Product Purpose

Dish Watch records dishes left out in a shared kitchen, starts a cleanup deadline (default 120 minutes), keeps photo and clip evidence, and maintains a corrected ledger of who left what and whether they washed it. Success is housemates being accountable on evidence they accept.

## Positioning

A camera over the counters with detection running on a Jetson Orin Nano in the kitchen. Footage, faces, and the ledger stay in the house. The camera proposes; a person confirms, until the camera's suggestions are measured accurate enough (30+ checks at 90%+). Then it names owners itself, with spot checks, and every name can be disputed.

## Operating Context

- Send is the home page. It holds one finished proof picture per dish still out past its deadline, found in the recording by the Jetson without anyone asking; a picture leaves when its dish does. Under each picture: one primary Send button (the Web Share sheet with the JPEG attached, which on an iPhone goes straight to Messages; Download where a browser cannot share files), then "Who put it down?" name chips in a fixed alphabetical order with the camera's guess outlined and, when the identity code gives one, its plain-English reason. Naming redraws the picture with the name and collapses the chips to that name, Change, and a link to the person's record. Not a dish and Wrong photos are quiet and offer Undo for five seconds; they are sent at once if the page is left or hidden. With nothing to send, the page says how many dishes are being checked in the footage or are out but under two hours.
- People ranks housemates by named proofs: a proof counts once someone picked that person's name on it, whether or not the dish was washed later; disputed, dismissed and rejected ones never count. One tap opens a person's pictures, newest first, each with Send. Proof photos are kept a week; older entries keep the date, dish and place without a picture. People with no named proofs are listed in one line. Face samples, personal links and adding people live on Housemates, in the menu.
- The proof picture (1600 px JPEG, under 600 KB, Mona Sans with a system-font fallback) is read as a small image in a chat thread. A dark canvas carries a large title ("Sam left this pan out", or "Pan left out" before anyone is named), the date and place, then four photos in a 2×2 grid: Before, Putting it down (or Leaving it, when the moment found is the person walking off from it), Left there, and Still there N hours later, each with its time. All four share one crop around the dish and the head and shoulders of whoever put it down; the dish always stays inside it. The dish spot is marked in yellow: dashed in Before (where it will be), corner brackets while it is being put down so the person is never covered, solid boxes after.
- Live kitchen opens the separate camera and investigation screen. The older Photos feed, photo records, Detection checks and Archive remain reachable from search and existing links but are not in the navigation; they confused more than they helped on a phone.
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
2. A finished result first. Show the complete picture and one clear Send action; keep collection progress and advanced review secondary. Corrections remain atomic, with explicit unknowns and protection against stale evidence.
3. Honest uncertainty. Unknown, unverified, and backlog states are shown as such, never rounded into guilt or success.
4. Local and private by default.
