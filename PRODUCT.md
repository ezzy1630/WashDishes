# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vanilla JavaScript ES modules and plain CSS served as static files by the FastAPI app (`wash_dishes/static`). No build step. Chosen with the user on 2026-10-09: the Jetson only serves files and the laptop browser renders, so a framework would add tooling without changing Jetson load. Strict CSP: `script-src 'self'`, no external fonts, CDNs, or inline scripts.

## Users

The household operator and housemates in one shared kitchen. Primary use is the operator's iPhone over Tailscale, in sessions of under a minute: open the dashboard, check weekly kitchen duty, review a few visits, and share the result to the house group chat. Name who used or left the dishes only when known. A laptop browser is used for setup and the live camera.

## Product Purpose

Dish Watch records dishes left out in a shared kitchen, starts a cleanup deadline (default 120 minutes), keeps photo and clip evidence, and maintains a corrected ledger of who left what and whether they washed it. Success is housemates being accountable on evidence they accept.

## Positioning

A camera over the counters with detection running on a Jetson Orin Nano in the kitchen. Footage, faces, and the ledger stay in the house. The camera proposes; a person confirms, until the camera's suggestions are measured accurate enough (30+ checks at 90%+). Then it names owners itself, with spot checks, and every name can be disputed.

## Operating Context

- Send is the operator home. A compact weekly block shows current kitchen duty, standings, the provisional next pick and the points/lead needed. A week selector retains open grace weeks and closed history. To review shows one visit at a time: its full-width six-frame arrival filmstrip on a phone, alphabetical name chips with an outlined camera suggestion, Not a dish, It's clean, Same as another (choose the original visit or dish), and Can't tell. Naming asks who **used or left** the dishes; nearby presence is insufficient. Missing arrivals say “Arrival not recorded. Only name someone if you know.” Answers advance immediately, with five seconds to Undo; pending answers flush on hide/navigation, failures restore the visit, and saved exclusions can be undone in Week’s record. Open disputes show settlement controls and paused points. Closed weeks cannot change.
- Ready to send holds a server-rendered 1080-wide weekly JPEG and any finished four-photo proof collages. The weekly image includes the date range, top three standings, other scorers when needed (never falsely saying everyone else has zero), the next duty result and its reason, the rule thresholds and https://highest-house.vercel.app. Open weeks are explicitly provisional; closed results retain their frozen duty outcome. Web Share receives a preloaded image; unsupported browsers download it. Sharing waits for pending review answers. Collages remain independently sendable, nameable and rejectable with Undo. Missing collages do not block visit review. Incomplete sequences, search progress and footage inspection remain on records/Live rather than the home.
- A quiet health line reports camera connection, last analysis and approximate retained footage hours (recording gaps excluded), with Inspect dishes in Live. The finished-proof block is omitted when empty. Offline, moved-camera, disabled detection and stale analysis states put a recovery link above the week. Home/People use compact status reads once a minute, never the full dashboard/history payload; full pages fetch their normal data on navigation.
- People ranks housemates by named proofs: a proof counts once someone picked that person's name on it, whether or not the dish was washed later; disputed, dismissed and rejected ones never count. One tap opens a person's pictures, newest first, each with Send. Proof photos are kept a week; older entries keep the date, dish and place without a picture. People with no named proofs are listed in one line. Face samples, personal links and adding people live on Housemates, in the menu.
- The proof picture (1600 px JPEG, under 600 KB, Mona Sans with a system-font fallback) is read as a small image in a chat thread. A dark canvas carries a large title ("Sam left this pan out", or "Pan left out" before anyone is named), the date and place, then four photos in a 2×2 grid: Before, Putting it down (or Leaving it, when the moment found is the person walking off from it), Left there, and Still there N hours later, each with its time. All four share one crop around the dish and the head and shoulders of whoever put it down; the dish always stays inside it. The dish spot is marked in yellow: dashed in Before (where it will be), corner brackets while it is being put down so the person is never covered, solid boxes after.
- Live kitchen opens the separate camera and investigation screen. The older Photos feed, photo records, Detection checks and Archive remain reachable from search and existing links but are not in the navigation; they confused more than they helped on a phone.
- Setup covers the camera connection, named monitoring areas (counters, sink, racks, cabinets) drawn on a snapshot, empty-area references for sink fullness, deadline, retention, and detection.
- Training: users correct detector boxes on saved frames to fine-tune a model for this kitchen.
- Demo mode runs with fictional housemates and synthetic evidence; it must always be labeled as demo.

## Capabilities and Constraints

- The house league uses Monday–Sunday deadline weeks in the house timezone, with
  Monday noon closing after a 12-hour naming grace. Verified late dishes score
  pan/pot 2, other 1, times quantity (editable). A unique leader must reach both
  minimum points 4 and lead 2 (editable) to get next week's kitchen duty. A
  no-pick close retains the current duty person. Switching duty off clears it;
  the operator can set duty after a house vote. Closed scores, names, rules and
  picks are immutable snapshots, including dishes later taken away unnamed.
- Naming is per visit, using arrival-filmstrip membership with singleton fallback.
  Any signed-in housemate may name an unnamed visit; camera guesses never count
  until confirmed. Only the named person disputes, pausing points. Another
  housemate, excluding the disputer and namer, or the operator settles. Visit
  changes and their events are atomic. Closed-week naming/disputes are refused.
- The review queue includes an unreviewed-visit count. Not a dish, It's clean and
  Same dish as another exclude a visit for everyone, attributed to the answerer
  and reversible by the operator. Can't tell hides it only for that resident and
  creates no label. Camera-assigned names also score zero until a person confirms
  responsibility; an eligible overdue observation alone is never a penalty.
- An optional public house site reads a narrow Jetson API with Supabase JWT
  sign-in and signed, expiring still images. It has no live video or money feature.
  Housemates provides one-time sign-in/password-reset links; no email is sent.
  Supabase owns credentials; resident mappings, dish history and media stay local.

- Statuses: pending, overdue (past deadline), review, disputed, resolved (washing confirmed or removed with washing unknown), cleared (taken away before the deadline), dismissed. Each detected dish in a dirty or sink area gets its own photo record, including startup inventory. Startup records retain unknown original departure and ownership; their two-hour follow-up starts at observation. Continued localized handling before the follow-up restarts that clock from the next unattended view. A fresh matching detection is required for the later photo, including while the surrounding area is busy. Earlier genuine captures are retained or recovered where available; missing history stays explicit. Rack or storage arrivals close the dirty episode without claiming washing. Missing-after-gap records are not currently-out dishes.
- Unknown ownership never erases an observed left-out dish. When an unnamed dish disappears after a matching, continuously observed deadline/follow-up crossing, its episode becomes resolved, with washing unknown and `left_out_at` retained. Startup inventory uses its existing observation-based two-hour follow-up; original deposit time stays unknown. Startup dishes removed before that follow-up retain their existing automatic dismissal. Auto-closures never teach the kitchen probe "not a dish".
- Camera episodes without a fresh sighting for 24 hours close as missing/unknown, including named, startup and archived records. This does not invent washing, an on-time success or a new late verdict. Earlier supported lateness remains historical. A resolved episode can therefore mean missing with an unknown outcome; it is not necessarily confirmed washing.
- Arrival review names a **visit**, once for all its member dishes. A continuous person visit at the same or adjacent spots can share one six-frame filmstrip; an empty interval, a recording gap, a different person or ambiguous person continuity splits visits. The strip marks every member dish, includes head/torso context and actual frame times, and remains separate from the precision-tuned four-photo Send proof. If the actual spot change cannot be recovered, the strip says “Already there at [time] — arrival not recorded”; a nearby visitor is not substituted. Coexisting overlapping duplicate records share one physical-dish slot and retain their alias ids for audit; they count once. The strip never confirms responsibility or washing. The visit carries the existing camera suggestion only when all members agree; corrections refresh that consensus without new identity inference. Filmstrips expire after seven days.
- A passed deadline without continuous observation is "unverified" and excluded from overdue statistics. Disputed and dismissed items are excluded from named statistics.
- Area roles: dirty (counters/landing areas), sink, clean (racks), storage (cabinets).
- Analysis rate depends on detector load on the Jetson; smooth video comes from a go2rtc relay as fragmented MP4. Frame-by-frame JPEG fallback exists.
- The Jetson also runs inference; the dashboard must keep polling and payloads modest.

## Evidence on Hand

The Jetson is the live source. Authorized isolated snapshots under `.data/` may contain private real household records and media; they are not demo or live validation. Demo data in `.data/demo` is synthetic. Never include private snapshots, faces, household labels or credentials in a source release.

## Product Principles

1. Show the evidence, then ask. Every attribution traces to a photo, clip, or a person's confirmation.
2. A useful decision first. Show the week and one evidence-backed visit to review, then shareable results; keep collection progress and advanced review secondary. Corrections remain atomic, with explicit unknowns and protection against stale evidence.
3. Honest uncertainty. Unknown, unverified, and backlog states are shown as such, never rounded into guilt or success.
4. Local and private by default.
